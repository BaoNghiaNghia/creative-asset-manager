from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.modules.assets.content_resolver import (
    SourceAssetContentResolver,
    SourceAssetContentTransient,
    SourceAssetContentUnavailable,
)
from app.modules.pipeline.errors import InvalidPipelineContent, TransientPipelineContent
from app.modules.pipeline.model import AssetPipelineModel
from app.providers.source_factory import create_source_provider

TokenResolver = Callable[[str], Awaitable[str]]
SourceProviderFactory = Callable[[str, str], Any]

class SourceAssetPipelineContentResolver:
    def __init__(self, session_factory: Callable[[], Session], *, token_resolver: TokenResolver | None = None, source_provider_factory: SourceProviderFactory = create_source_provider):
        self.resolver = SourceAssetContentResolver(session_factory, token_resolver=token_resolver, source_provider_factory=source_provider_factory)

    @asynccontextmanager
    async def open(self, *, tenant_id: str, pipeline: AssetPipelineModel):
        if pipeline.tenant_id != tenant_id or not pipeline.source_asset_id:
            raise InvalidPipelineContent("source asset is unavailable")
        try:
            async with self.resolver.open(tenant_id=tenant_id, source_asset_id=pipeline.source_asset_id) as stream:
                yield stream
        except SourceAssetContentTransient as exc:
            raise TransientPipelineContent(str(exc)) from exc
        except SourceAssetContentUnavailable as exc:
            raise InvalidPipelineContent(str(exc)) from exc
