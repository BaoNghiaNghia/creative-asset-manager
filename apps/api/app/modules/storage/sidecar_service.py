from __future__ import annotations

from app.domain.providers.contracts import (
    AssetStorageProvider,
    StorageProviderError,
    StoreMetadataSidecarInput,
    StoredMetadataSidecar,
)
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.storage.sidecar_document import MetadataSidecarDocumentBuilder
from app.modules.storage.sidecar_repository import MetadataSidecarRepository


class MetadataSidecarExportService:
    def __init__(
        self,
        repository: MetadataSidecarRepository,
        document_builder: MetadataSidecarDocumentBuilder,
        *,
        enabled: bool = False,
        max_attempts: int = 5,
    ):
        if repository.session is not document_builder.session:
            raise ValueError("sidecar repository and builder must share one session")
        self.repository = repository
        self.document_builder = document_builder
        self.enabled = enabled
        self.max_attempts = max_attempts

    async def export(
        self,
        *,
        tenant_id: str,
        analysis_id: str,
        provider: AssetStorageProvider,
    ) -> StoredMetadataSidecar:
        if not self.enabled:
            raise RuntimeError("Drive metadata sidecar export is disabled")
        analysis = self.repository.session.get(AssetAiAnalysisModel, analysis_id)
        if analysis is None or analysis.tenant_id != tenant_id:
            raise LookupError(analysis_id)
        document, document_hash = self.document_builder.build(tenant_id, analysis_id)
        provider_name = getattr(provider, "provider_name", provider.__class__.__name__)
        record = self.repository.get_or_create(
            tenant_id=tenant_id,
            asset_id=analysis.asset_id,
            analysis_id=analysis.id,
            storage_provider=provider_name,
            document_hash=document_hash,
        )
        if record.status == "stored" and record.document_hash == document_hash:
            return StoredMetadataSidecar(
                storage_key=record.storage_key or "",
                remote_file_id=record.remote_file_id,
                remote_folder_id=record.remote_folder_id,
                web_url=record.web_url,
                document_hash=record.document_hash,
            )

        self.repository.mark_exporting(record)
        self.repository.session.commit()
        try:
            result = await provider.store_metadata_sidecar(
                StoreMetadataSidecarInput(
                    tenant_id=tenant_id,
                    asset_id=analysis.asset_id,
                    analysis_id=analysis.id,
                    metadata=document,
                    document_hash=document_hash,
                )
            )
            if not result.remote_file_id:
                raise StorageProviderError(
                    "storage provider returned no sidecar file ID",
                    retryable=True,
                )
            self.repository.mark_stored(
                record,
                storage_key=result.storage_key,
                remote_file_id=result.remote_file_id,
                remote_folder_id=result.remote_folder_id,
                web_url=result.web_url,
            )
            self.repository.session.commit()
            return result
        except StorageProviderError as exc:
            self.repository.mark_failure(
                record,
                retryable=exc.retryable,
                error_code=type(exc).__name__,
                error_message=str(exc),
                max_attempts=self.max_attempts,
            )
            self.repository.session.commit()
            raise
        except Exception as exc:
            self.repository.mark_failure(
                record,
                retryable=True,
                error_code=type(exc).__name__,
                error_message=str(exc),
                max_attempts=self.max_attempts,
            )
            self.repository.session.commit()
            raise
