from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.modules.assets.content_resolver import SourceAssetContentResolver, SourceAssetContentUnavailable
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.stages import InvalidPipelineContent
from app.providers.google.auth import get_connection_access_token
from app.providers.source_factory import create_source_provider

TokenResolver = Callable[[str], Awaitable[str]]
SourceProviderFactory = Callable[[str, str], Any]

class SourceAssetPipelineContentResolver:
    def __init__(self, session_factory: Callable[[], Session], *, token_resolver: TokenResolver = get_connection_access_token, source_provider_factory: SourceProviderFactory = create_source_provider):
        self.resolver = SourceAssetContentResolver(session_factory, token_resolver=token_resolver, source_provider_factory=source_provider_factory)

    @asynccontextmanager
    async def open(self, *, tenant_id: str, pipeline: AssetPipelineModel):
        if pipeline.tenant_id != tenant_id or not pipeline.source_asset_id:
            raise InvalidPipelineContent("source asset is unavailable")
        try:
            async with self.resolver.open(tenant_id=tenant_id, source_asset_id=pipeline.source_asset_id) as stream:
                yield stream
        except SourceAssetContentUnavailable as exc:
            raise InvalidPipelineContent(str(exc)) from exc
