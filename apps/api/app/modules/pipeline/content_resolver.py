from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.providers.contracts import AssetDownloadStream, OpenSourceAssetInput
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.stages import InvalidPipelineContent
from app.providers.google.auth import get_connection_access_token
from app.providers.source_factory import create_source_provider


TokenResolver = Callable[[str], Awaitable[str]]
SourceProviderFactory = Callable[[str, str], Any]


class SourceAssetPipelineContentResolver:
    """Resolve source bytes from a tenant-owned source asset and OAuth connection."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        token_resolver: TokenResolver = get_connection_access_token,
        source_provider_factory: SourceProviderFactory = create_source_provider,
    ):
        self.session_factory = session_factory
        self.token_resolver = token_resolver
        self.source_provider_factory = source_provider_factory

    @asynccontextmanager
    async def open(
        self,
        *,
        tenant_id: str,
        pipeline: AssetPipelineModel,
    ):
        if pipeline.tenant_id != tenant_id or not pipeline.source_asset_id:
            raise InvalidPipelineContent("source asset is unavailable")

        with self.session_factory() as session:
            row = session.execute(
                select(SourceAssetModel, ExternalSourceModel)
                .join(
                    ExternalSourceModel,
                    (
                        ExternalSourceModel.id
                        == SourceAssetModel.external_source_id
                    )
                    & (
                        ExternalSourceModel.tenant_id
                        == SourceAssetModel.tenant_id
                    ),
                )
                .where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.id == pipeline.source_asset_id,
                    SourceAssetModel.deleted_at.is_(None),
                    ExternalSourceModel.tenant_id == tenant_id,
                )
            ).one_or_none()
            if row is None:
                raise InvalidPipelineContent("source asset is unavailable")
            source_asset, source = row
            source_type = source.source_type
            source_id = source.id
            external_asset_id = source_asset.external_asset_id
            connection_id = (source.source_metadata or {}).get(
                "oauth_connection_id"
            )

        if source_type != "google_drive":
            raise InvalidPipelineContent("source provider is unsupported")
        if not isinstance(connection_id, str) or not connection_id:
            raise InvalidPipelineContent("source OAuth connection is unavailable")

        try:
            access_token = await self.token_resolver(connection_id)
        except Exception as exc:
            raise InvalidPipelineContent(
                "source OAuth connection is unavailable"
            ) from exc

        provider = self.source_provider_factory("google-drive", access_token)
        async with provider:
            stream = await provider.open_download_stream(
                OpenSourceAssetInput(
                    source_id=source_id,
                    external_asset_id=external_asset_id,
                )
            )
            closed = False

            async def close() -> None:
                nonlocal closed
                if closed:
                    return
                closed = True
                await stream.close()

            guarded_stream = AssetDownloadStream(
                body=stream.body,
                close=close,
                status_code=stream.status_code,
                content_type=stream.content_type,
                headers=stream.headers,
            )
            try:
                yield guarded_stream
            finally:
                await close()
