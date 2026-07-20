from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncContextManager, Protocol

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.domain.providers.contracts import AssetDownloadStream, AssetStorageProvider, StoreAssetInput
from app.modules.assets.content_dedup_service import ContentDeduplicationService
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.handlers import DownloadStageResult
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.storage.repository import ManagedStorageRepository
from app.modules.storage.service import ManagedAssetStorageService


class PipelineContentResolver(Protocol):
    def open(self, *, tenant_id: str, pipeline: AssetPipelineModel) -> AsyncContextManager[AssetDownloadStream]: ...


class InvalidPipelineContent(ValueError):
    pass


class ProviderDownloadStage:
    """Bounded, temporary-file download + validation + authoritative SHA dedup."""

    def __init__(self, session_factory: Callable[[], Session], resolver: PipelineContentResolver, *,
                 max_bytes: int = 25_000_000, max_pixels: int = 80_000_000,
                 temp_directory: str | None = None):
        self.session_factory = session_factory
        self.resolver = resolver
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.temp_directory = temp_directory

    async def execute(self, *, tenant_id: str, pipeline: AssetPipelineModel) -> DownloadStageResult:
        path: Path | None = None
        stream: AssetDownloadStream | None = None
        try:
            async with self.resolver.open(tenant_id=tenant_id, pipeline=pipeline) as stream:
                path, size = await self._bounded_copy(stream.body)
                mime_type = self._validate(path, stream.content_type)
            with self.session_factory() as session:
                source_asset_id = pipeline.source_asset_id
                if not source_asset_id:
                    raise InvalidPipelineContent("download resolver did not attach a source asset")
                repository = AssetRegistryRepository(session)
                before = repository.find_linked_asset(tenant_id, source_asset_id)
                result = await ContentDeduplicationService(repository, enabled=True).ingest(
                    tenant_id=tenant_id, source_asset_id=source_asset_id,
                    content_stream=self._file_chunks(path), mime_type=mime_type,
                )
                return DownloadStageResult(
                    source_asset_id=source_asset_id, asset_id=result.asset.id,
                    content_hash=result.asset.content_hash,
                    duplicate=result.reused_asset and (before is None or before.id != result.asset.id),
                )
        finally:
            if stream is not None:
                await stream.close()
            if path is not None:
                path.unlink(missing_ok=True)

    async def _bounded_copy(self, body: AsyncIterator[bytes]) -> tuple[Path, int]:
        descriptor, name = tempfile.mkstemp(prefix="cam-pipeline-", suffix=".asset", dir=self.temp_directory)
        os.close(descriptor)
        path = Path(name)
        size = 0
        try:
            with path.open("wb") as output:
                async for chunk in body:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise InvalidPipelineContent("source content exceeds configured byte limit")
                    output.write(chunk)
            if size == 0:
                raise InvalidPipelineContent("source content is empty")
            return path, size
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _validate(self, path: Path, declared_type: str) -> str:
        with path.open("rb") as source:
            header = source.read(16)
        image = header.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
        video = len(header) >= 12 and header[4:8] == b"ftyp"
        if image:
            try:
                with Image.open(path) as decoded:
                    width, height = decoded.size
                    if width * height > self.max_pixels:
                        raise InvalidPipelineContent("image exceeds configured pixel limit")
                    decoded.verify()
            except (UnidentifiedImageError, OSError) as exc:
                raise InvalidPipelineContent("image decode validation failed") from exc
            return declared_type if declared_type.startswith("image/") else "image/unknown"
        if video:
            return declared_type if declared_type.startswith("video/") else "video/mp4"
        raise InvalidPipelineContent("unsupported file signature")

    @staticmethod
    async def _file_chunks(path: Path, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk


class ProviderStorageStage:
    """Reopens authoritative source bytes and delegates idempotent storage."""

    def __init__(self, session_factory: Callable[[], Session], resolver: PipelineContentResolver,
                 provider: AssetStorageProvider):
        self.session_factory = session_factory
        self.resolver = resolver
        self.provider = provider

    async def execute(self, *, tenant_id: str, pipeline: AssetPipelineModel) -> None:
        if not pipeline.asset_id or not pipeline.content_hash:
            raise InvalidPipelineContent("pipeline asset identity is incomplete")
        async with self.resolver.open(tenant_id=tenant_id, pipeline=pipeline) as stream:
            try:
                with self.session_factory() as session:
                    await ManagedAssetStorageService(
                        AssetRegistryRepository(session), ManagedStorageRepository(session), enabled=True,
                    ).store(
                        StoreAssetInput(
                            tenant_id=tenant_id, asset_id=pipeline.asset_id,
                            content_hash=pipeline.content_hash, body=stream.body,
                            content_type=stream.content_type,
                        ),
                        self.provider,
                    )
            finally:
                await stream.close()
