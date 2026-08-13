from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.inventory.drive.downloader import InventoryFileDownloader
from app.modules.inventory.drive.poller import INVENTORY_FILE_DOWNLOAD_JOB
from app.modules.inventory.drive.storage import InventorySourceStorage
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.preparation.image import InventoryImagePreparationLimits, StatelessInventoryImagePreparer
from app.modules.inventory.preparation.service import InventoryDocumentPreparer, INVENTORY_DOCUMENT_PREPARE_JOB
from app.modules.inventory.preparation.storage import InventoryPreparedStorage
from app.modules.inventory.ai.service import INVENTORY_DOCUMENT_ANALYZE_JOB, InventoryDocumentAnalyzer
from app.modules.inventory.ai.gateway import RuntimeInventoryGeminiGateway
from app.modules.inventory.credentials import InventoryGeminiCredentialResolver
from app.modules.inventory.transactions.service import INVENTORY_DOCUMENT_COMMIT_JOB, InventoryDocumentCommitter
from app.modules.inventory.documents.service import (
    INVENTORY_DOCUMENT_NORMALIZE_JOB, INVENTORY_DOCUMENT_VALIDATE_JOB,
    InventoryDocumentNormalizer, InventoryDocumentValidator,
)

InventoryJobHandler = Callable[[InventoryJobModel], None]


class InventoryHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, InventoryJobHandler] = {}

    def register(self, job_type: str, handler: InventoryJobHandler) -> None:
        if not job_type or not job_type.startswith("inventory_"):
            raise ValueError("Inventory job types must use the inventory_ namespace")
        if job_type in self._handlers:
            raise ValueError(f"Inventory handler already registered for {job_type}")
        self._handlers[job_type] = handler

    def resolve(self, job_type: str) -> InventoryJobHandler | None:
        return self._handlers.get(job_type)

    @property
    def job_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)


def build_inventory_handler_registry(
    settings: Settings | None = None,
    *,
    downloader: InventoryFileDownloader | None = None,
    document_preparer: InventoryDocumentPreparer | None = None,
    document_analyzer: InventoryDocumentAnalyzer | None = None,
    document_normalizer: InventoryDocumentNormalizer | None = None,
    document_validator: InventoryDocumentValidator | None = None,
    document_committer: InventoryDocumentCommitter | None = None,
) -> InventoryHandlerRegistry:
    """Register only handlers delivered by completed Inventory phases."""
    runtime_settings = settings or Settings()
    runtime_downloader = downloader or InventoryFileDownloader(
        SessionLocal,
        storage=InventorySourceStorage(
            runtime_settings.INVENTORY_SOURCE_STORAGE_ROOT
        ),
        max_bytes=runtime_settings.INVENTORY_DOWNLOAD_MAX_BYTES,
    )
    runtime_preparer = document_preparer or InventoryDocumentPreparer(
        SessionLocal,
        source_storage=InventoryPreparedStorage(runtime_settings.INVENTORY_SOURCE_STORAGE_ROOT),
        prepared_storage=InventoryPreparedStorage(runtime_settings.INVENTORY_SOURCE_STORAGE_ROOT),
        image_preparer=StatelessInventoryImagePreparer(
            InventoryImagePreparationLimits(
                max_source_bytes=runtime_settings.INVENTORY_PREPARE_MAX_SOURCE_BYTES,
                max_source_width=runtime_settings.INVENTORY_PREPARE_MAX_SOURCE_WIDTH,
                max_source_height=runtime_settings.INVENTORY_PREPARE_MAX_SOURCE_HEIGHT,
                max_decode_pixels=runtime_settings.INVENTORY_PREPARE_MAX_DECODE_PIXELS,
                max_output_bytes=runtime_settings.INVENTORY_PREPARE_MAX_OUTPUT_BYTES,
                max_width=runtime_settings.INVENTORY_PREPARE_MAX_WIDTH,
                max_height=runtime_settings.INVENTORY_PREPARE_MAX_HEIGHT,
                jpeg_quality=runtime_settings.INVENTORY_PREPARE_JPEG_QUALITY,
            )
        ),
    )
    runtime_analyzer = document_analyzer or InventoryDocumentAnalyzer(
        SessionLocal,
        prepared_storage=InventoryPreparedStorage(runtime_settings.INVENTORY_SOURCE_STORAGE_ROOT),
        gateway=RuntimeInventoryGeminiGateway(InventoryGeminiCredentialResolver(SessionLocal, runtime_settings), timeout_seconds=runtime_settings.INVENTORY_AI_TIMEOUT_SECONDS),
        enabled=runtime_settings.INVENTORY_AI_ENABLED,
    )
    runtime_normalizer = document_normalizer or InventoryDocumentNormalizer(SessionLocal)
    runtime_validator = document_validator or InventoryDocumentValidator(SessionLocal)
    runtime_committer = document_committer or InventoryDocumentCommitter(
        SessionLocal, shadow_mode=runtime_settings.INVENTORY_SHADOW_MODE
    )
    registry = InventoryHandlerRegistry()

    def download(job: InventoryJobModel) -> None:
        asyncio.run(runtime_downloader.execute(job))

    def prepare(job: InventoryJobModel) -> None:
        runtime_preparer.execute(job)

    def analyze(job: InventoryJobModel) -> None:
        runtime_analyzer.execute(job)

    def normalize(job: InventoryJobModel) -> None:
        runtime_normalizer.execute(job)

    def validate(job: InventoryJobModel) -> None:
        runtime_validator.execute(job)

    def commit(job: InventoryJobModel) -> None:
        runtime_committer.execute(job)

    registry.register(INVENTORY_FILE_DOWNLOAD_JOB, download)
    registry.register(INVENTORY_DOCUMENT_PREPARE_JOB, prepare)
    registry.register(INVENTORY_DOCUMENT_ANALYZE_JOB, analyze)
    registry.register(INVENTORY_DOCUMENT_NORMALIZE_JOB, normalize)
    registry.register(INVENTORY_DOCUMENT_VALIDATE_JOB, validate)
    registry.register(INVENTORY_DOCUMENT_COMMIT_JOB, commit)
    return registry
