from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.jobs.errors import InventoryJobFailure
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import (
    InventoryDocumentModel,
    InventoryDocumentPageModel,
    InventorySourceFileModel,
    inventory_utcnow,
)
from app.modules.inventory.preparation.image import (
    InventoryImagePreparationError,
    StatelessInventoryImagePreparer,
)
from app.modules.inventory.preparation.storage import InventoryPreparedStorage


INVENTORY_DOCUMENT_PREPARE_JOB = "inventory_document_prepare"
PREPARATION_VERSION = 1
logger = logging.getLogger("cam.inventory.preparation")


class InventoryPrepareFailure(InventoryJobFailure):
    pass


class InventoryDocumentPreparer:
    """Inventory-only document/page preparation; no Creative models or jobs are used."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        source_storage: InventoryPreparedStorage,
        prepared_storage: InventoryPreparedStorage,
        image_preparer: StatelessInventoryImagePreparer,
    ):
        self.session_factory = session_factory
        self.source_storage = source_storage
        self.prepared_storage = prepared_storage
        self.image_preparer = image_preparer

    def execute(self, job: InventoryJobModel) -> None:
        source_file_id = str((job.payload_json or {}).get("source_file_id") or job.entity_id)
        source = self._begin(job.tenant_id, source_file_id)
        if source is None:
            return
        try:
            existing = self._prepared_page(source.tenant_id, source.id)
            if existing is not None:
                self._mark_existing(source.tenant_id, source.id, existing)
                return
            canonical = self._canonical_prepared_page(source)
            if canonical is not None:
                self._persist(source, duplicate_of=canonical, prepared=None)
                return
            if not source.storage_key or not source.content_sha256:
                raise InventoryPrepareFailure("inventory_prepare_source_identity_missing", retryable=False)
            source_path = self.source_storage.source_path(
                tenant_id=source.tenant_id, storage_key=source.storage_key
            )
            prepared = self.image_preparer.prepare(
                source_path,
                expected_sha256=source.content_sha256,
                expected_size=source.drive_size,
            )
            storage_key = self.prepared_storage.write_atomic(
                tenant_id=source.tenant_id,
                source_file_id=source.id,
                preparation_version=source.preparation_version,
                content_hash=prepared.content_sha256,
                content=prepared.content,
            )
            self._persist(source, duplicate_of=None, prepared=(prepared, storage_key))
        except InventoryPrepareFailure as exc:
            self._record_failure(source.tenant_id, source.id, exc)
            raise
        except InventoryImagePreparationError as exc:
            failure = InventoryPrepareFailure(exc.code, retryable=exc.retryable)
            self._record_failure(source.tenant_id, source.id, failure)
            raise failure from exc
        except (OSError, RuntimeError) as exc:
            code = str(exc) if str(exc).startswith("inventory_") else "inventory_prepare_storage_failure"
            failure = InventoryPrepareFailure(code, retryable=code.endswith("storage_missing") or code.endswith("storage_failure"))
            self._record_failure(source.tenant_id, source.id, failure)
            raise failure from exc
        except Exception as exc:
            failure = InventoryPrepareFailure("inventory_prepare_unexpected", retryable=True)
            self._record_failure(source.tenant_id, source.id, failure)
            raise failure from exc

    def _begin(self, tenant_id: str, source_file_id: str) -> InventorySourceFileModel | None:
        with self.session_factory() as session:
            query = select(InventorySourceFileModel).where(
                InventorySourceFileModel.tenant_id == tenant_id,
                InventorySourceFileModel.id == source_file_id,
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            source = session.scalar(query)
            if source is None:
                raise InventoryPrepareFailure("inventory_prepare_source_not_found", retryable=False)
            if source.status not in {"downloaded", "duplicate"}:
                raise InventoryPrepareFailure("inventory_prepare_source_not_downloaded", retryable=False)
            if source.preparation_status in {"prepared", "duplicate"}:
                return None
            source.preparation_status = "preparing"
            source.preparation_error_code = None
            source.preparation_error_message = None
            session.commit()
            session.expunge(source)
            return source

    def _prepared_page(self, tenant_id: str, source_file_id: str) -> InventoryDocumentPageModel | None:
        with self.session_factory() as session:
            return session.scalar(
                select(InventoryDocumentPageModel).where(
                    InventoryDocumentPageModel.tenant_id == tenant_id,
                    InventoryDocumentPageModel.source_file_id == source_file_id,
                    InventoryDocumentPageModel.preparation_status.in_(("prepared", "duplicate")),
                )
            )

    def _canonical_prepared_page(self, source: InventorySourceFileModel) -> InventoryDocumentPageModel | None:
        if not source.duplicate_of_source_file_id:
            return None
        with self.session_factory() as session:
            return session.scalar(
                select(InventoryDocumentPageModel).where(
                    InventoryDocumentPageModel.tenant_id == source.tenant_id,
                    InventoryDocumentPageModel.source_file_id == source.duplicate_of_source_file_id,
                    InventoryDocumentPageModel.preparation_status == "prepared",
                )
            )

    def _document(self, session: Session, source: InventorySourceFileModel) -> InventoryDocumentModel:
        key = f"inventory-document-prepare:v{source.preparation_version}:{source.id}"
        existing = session.scalar(
            select(InventoryDocumentModel).where(
                InventoryDocumentModel.tenant_id == source.tenant_id,
                InventoryDocumentModel.idempotency_key == key,
            )
        )
        if existing is not None:
            return existing
        try:
            with session.begin_nested():
                document = InventoryDocumentModel(
                    tenant_id=source.tenant_id,
                    idempotency_key=key,
                    business_date=None,
                    document_type="unclassified",
                    location_id=None,
                    status="preparing",
                    expected_pages=1,
                    received_pages=0,
                )
                session.add(document)
                session.flush()
            return document
        except IntegrityError:
            existing = session.scalar(
                select(InventoryDocumentModel).where(
                    InventoryDocumentModel.tenant_id == source.tenant_id,
                    InventoryDocumentModel.idempotency_key == key,
                )
            )
            if existing is None:
                raise
            return existing

    def _persist(self, source: InventorySourceFileModel, *, duplicate_of: InventoryDocumentPageModel | None, prepared) -> None:
        with self.session_factory() as session:
            current = session.scalar(
                select(InventorySourceFileModel).where(
                    InventorySourceFileModel.tenant_id == source.tenant_id,
                    InventorySourceFileModel.id == source.id,
                )
            )
            if current is None:
                raise InventoryPrepareFailure("inventory_prepare_source_not_found", retryable=False)
            document = self._document(session, current)
            page = session.scalar(
                select(InventoryDocumentPageModel).where(
                    InventoryDocumentPageModel.tenant_id == current.tenant_id,
                    InventoryDocumentPageModel.source_file_id == current.id,
                )
            )
            is_duplicate = duplicate_of is not None
            if page is None:
                try:
                    with session.begin_nested():
                        page = InventoryDocumentPageModel(
                            tenant_id=current.tenant_id,
                            document_id=document.id,
                            source_file_id=current.id,
                            drive_file_id=current.drive_file_id,
                            page_number=1,
                            page_count=1,
                            content_sha256=current.content_sha256,
                            duplicate_of_page_id=duplicate_of.id if duplicate_of else None,
                            preparation_version=current.preparation_version,
                            preparation_status="duplicate" if is_duplicate else "prepared",
                            prepared_storage_key=(duplicate_of.prepared_storage_key if is_duplicate else prepared[1]),
                            prepared_content_sha256=(duplicate_of.prepared_content_sha256 if is_duplicate else prepared[0].content_sha256),
                            prepared_size_bytes=(duplicate_of.prepared_size_bytes if is_duplicate else len(prepared[0].content)),
                            prepared_mime_type=(duplicate_of.prepared_mime_type if is_duplicate else prepared[0].mime_type),
                            image_width=(duplicate_of.image_width if is_duplicate else prepared[0].width),
                            image_height=(duplicate_of.image_height if is_duplicate else prepared[0].height),
                        )
                        session.add(page)
                        session.flush()
                except IntegrityError:
                    page = session.scalar(
                        select(InventoryDocumentPageModel).where(
                            InventoryDocumentPageModel.tenant_id == current.tenant_id,
                            InventoryDocumentPageModel.source_file_id == current.id,
                        )
                    )
                    if page is None:
                        raise
            document.received_pages = 1
            document.status = "duplicate" if is_duplicate else "prepared"
            current.preparation_status = "duplicate" if is_duplicate else "prepared"
            current.preparation_error_code = None
            current.preparation_error_message = None
            session.commit()
            logger.info(
                "inventory_document_prepared tenant_id=%s source_file_id=%s document_id=%s page_id=%s duplicate=%s",
                current.tenant_id, current.id, document.id, page.id, is_duplicate,
            )

    def _mark_existing(self, tenant_id: str, source_file_id: str, page: InventoryDocumentPageModel) -> None:
        with self.session_factory() as session:
            source = session.scalar(select(InventorySourceFileModel).where(
                InventorySourceFileModel.tenant_id == tenant_id,
                InventorySourceFileModel.id == source_file_id,
            ))
            if source is None:
                return
            source.preparation_status = page.preparation_status
            source.preparation_error_code = None
            source.preparation_error_message = None
            session.commit()

    def _record_failure(self, tenant_id: str, source_file_id: str, failure: InventoryPrepareFailure) -> None:
        status = "retryable_failure" if failure.retryable else "terminal_failure"
        with self.session_factory() as session:
            source = session.scalar(select(InventorySourceFileModel).where(
                InventorySourceFileModel.tenant_id == tenant_id,
                InventorySourceFileModel.id == source_file_id,
            ))
            if source is None:
                return
            source.preparation_status = status
            source.preparation_error_code = failure.code[:100]
            source.preparation_error_message = failure.code[:1000]
            document = session.scalar(select(InventoryDocumentModel).where(
                InventoryDocumentModel.tenant_id == tenant_id,
                InventoryDocumentModel.idempotency_key == f"inventory-document-prepare:v{source.preparation_version}:{source.id}",
            ))
            if document is not None:
                document.status = status
            session.commit()
        logger.warning(
            "inventory_document_prepare_failed tenant_id=%s source_file_id=%s error_code=%s retryable=%s",
            tenant_id, source_file_id, failure.code, failure.retryable,
        )
