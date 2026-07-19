from __future__ import annotations

import hashlib
import json
from datetime import timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.modules.external_ingestion.model import AssetIngestionModel, ExternalApiCredentialModel
from app.modules.external_ingestion.repository import (
    ExternalIngestionRepository,
    IdempotencyConflictError,
)
from app.modules.external_ingestion.schema import (
    MAX_INGESTION_PAYLOAD_BYTES,
    AssetIngestionRequest,
)
from app.modules.processing.repository import ProcessingRepository


def canonical_request(request: AssetIngestionRequest) -> tuple[dict[str, Any], bytes, str]:
    document = {
        "source_id": request.source_id,
        "items": [
            {
                "external_asset_id": item.external_asset_id,
                "download_url": item.download_url,
                "checksum": item.checksum,
                "filename": item.filename,
                "modified_at": (
                    item.modified_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if item.modified_at
                    else None
                ),
            }
            for item in request.items
        ],
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return document, encoded, hashlib.sha256(encoded).hexdigest()


class ExternalIngestionService:
    def __init__(
        self,
        repository: ExternalIngestionRepository,
        processing: ProcessingRepository,
        *,
        max_payload_bytes: int = MAX_INGESTION_PAYLOAD_BYTES,
    ):
        if repository.session is not processing.session:
            raise ValueError("ingestion and processing repositories must share one transaction")
        self.repository = repository
        self.processing = processing
        self.max_payload_bytes = max_payload_bytes

    def create(
        self,
        *,
        credential: ExternalApiCredentialModel,
        idempotency_key: str,
        request: AssetIngestionRequest,
    ) -> AssetIngestionModel:
        if request.source_id != credential.external_source_id:
            raise PermissionError("API credential is not authorized for this source")
        source = self.repository.get_source(credential.tenant_id, request.source_id)
        if source is None or source.source_type != "external_api":
            raise PermissionError("external API source is not authorized")
        document, encoded, request_hash = canonical_request(request)
        if len(encoded) > self.max_payload_bytes:
            raise ValueError("canonical request exceeds the payload limit")
        existing = self.repository.get_by_idempotency_key(
            tenant_id=credential.tenant_id,
            external_source_id=request.source_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError(idempotency_key)
            return existing
        item_documents = [
            {
                **item,
                "modified_at": request.items[index].modified_at,
            }
            for index, item in enumerate(document["items"])
        ]
        try:
            ingestion, items = self.repository.create_ingestion(
                tenant_id=credential.tenant_id,
                external_source_id=request.source_id,
                credential_id=credential.id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                request_json=document,
                items=item_documents,
            )
            for item in items:
                job = self.processing.create_job(
                    tenant_id=credential.tenant_id,
                    job_type="source_asset_download",
                    entity_type="asset_ingestion_item",
                    entity_id=item.id,
                    idempotency_key=f"external-ingestion-item:{item.id}",
                    payload={
                        "ingestion_id": ingestion.id,
                        "ingestion_item_id": item.id,
                        "external_source_id": request.source_id,
                        "external_asset_id": item.external_asset_id,
                        "download_url": item.download_url,
                        "provider_checksum": item.provider_checksum,
                        "filename": item.filename,
                        "source_modified_at": (
                            item.source_modified_at.isoformat() if item.source_modified_at else None
                        ),
                    },
                )
                item.processing_job_id = job.id
            self.repository.session.commit()
            return ingestion
        except IntegrityError:
            self.repository.session.rollback()
            return self.repository.recover_idempotency_conflict(
                tenant_id=credential.tenant_id,
                external_source_id=request.source_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except Exception:
            self.repository.session.rollback()
            raise
