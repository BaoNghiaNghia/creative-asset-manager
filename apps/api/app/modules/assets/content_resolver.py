from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.providers.contracts import (
    AssetDownloadStream,
    GetSourceAssetInput,
    OpenSourceAssetInput,
)
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.assets.source_credentials import source_credential_contract
from app.modules.explorer.tenant_source import TenantSourceResolver
from app.providers.source_factory import create_source_provider


TokenResolver = Callable[[str], Awaitable[str]]
SourceProviderFactory = Callable[[str, str], Any]


class SourceAssetContentUnavailable(ValueError):
    """A tenant-scoped source asset cannot be streamed safely."""


class SourceAssetContentTransient(SourceAssetContentUnavailable):
    """A source provider failed temporarily and the stream can be retried."""


class SourceAssetContentResolver:
    """Open a provider stream for a tenant-owned source asset without buffering it."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        token_resolver: TokenResolver | None = None,
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
        source_asset_id: str,
        range_header: str | None = None,
    ):
        with self.session_factory() as session:
            row = session.execute(
                select(SourceAssetModel, ExternalSourceModel)
                .join(
                    ExternalSourceModel,
                    (ExternalSourceModel.id == SourceAssetModel.external_source_id)
                    & (ExternalSourceModel.tenant_id == SourceAssetModel.tenant_id),
                )
                .where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.id == source_asset_id,
                    ExternalSourceModel.tenant_id == tenant_id,
                )
            ).one_or_none()
            if row is None:
                raise SourceAssetContentUnavailable("source asset is unavailable")
            source_asset, source = row
            source_type = source.source_type
            source_id = source.id
            external_asset_id = source_asset.external_asset_id
            source_asset_deleted = source_asset.deleted_at is not None
            connection_id = source.oauth_connection_id

        try:
            contract = source_credential_contract(source_type)
        except ValueError as exc:
            raise SourceAssetContentUnavailable("source provider is unsupported") from exc
        if not isinstance(connection_id, str) or not connection_id:
            raise SourceAssetContentUnavailable("source OAuth connection is unavailable")
        try:
            if self.token_resolver is not None:
                access_token = await self.token_resolver(connection_id)
            else:
                with self.session_factory() as session:
                    resolved = await TenantSourceResolver(session).resolve(
                        tenant_id=tenant_id,
                        external_source_id=source_id,
                    )
                access_token = resolved.access_token
        except Exception as exc:
            raise SourceAssetContentUnavailable("source OAuth connection is unavailable") from exc

        provider = self.source_provider_factory(contract.adapter_key, access_token)
        async with provider:
            if source_asset_deleted:
                try:
                    candidate = await provider.get_asset(GetSourceAssetInput(
                        source_id=source_id,
                        external_asset_id=external_asset_id,
                    ))
                except Exception as exc:
                    raise SourceAssetContentUnavailable("source asset is unavailable") from exc
                if candidate.external_asset_id != external_asset_id:
                    raise SourceAssetContentUnavailable("source asset identity changed")

                refreshed_metadata = dict(candidate.source_metadata or {})
                parent_id = refreshed_metadata.get("parent_id")
                if isinstance(parent_id, str) and parent_id:
                    refreshed_metadata["parents"] = [parent_id]
                with self.session_factory() as session:
                    refreshed = session.scalar(select(SourceAssetModel).where(
                        SourceAssetModel.tenant_id == tenant_id,
                        SourceAssetModel.id == source_asset_id,
                        SourceAssetModel.external_source_id == source_id,
                        SourceAssetModel.external_asset_id == external_asset_id,
                    ))
                    if refreshed is None:
                        raise SourceAssetContentUnavailable("source asset is unavailable")
                    refreshed.filename = candidate.filename
                    refreshed.mime_type = candidate.mime_type
                    refreshed.size_bytes = candidate.size_bytes
                    refreshed.source_metadata = {
                        **dict(refreshed.source_metadata or {}),
                        **refreshed_metadata,
                    }
                    refreshed.deleted_at = None
                    session.commit()

            try:
                stream = await provider.open_download_stream(OpenSourceAssetInput(
                    source_id=source_id,
                    external_asset_id=external_asset_id,
                    range_header=range_header,
                ))
            except Exception as exc:
                if self._is_transient_provider_error(exc):
                    raise SourceAssetContentTransient(
                        "source provider is temporarily unavailable"
                    ) from exc
                raise SourceAssetContentUnavailable(
                    "source provider rejected the download request"
                ) from exc
            closed = False

            async def close() -> None:
                nonlocal closed
                if not closed:
                    closed = True
                    await stream.close()

            async def body():
                try:
                    async for block in stream.body:
                        yield block
                except Exception as exc:
                    if self._is_transient_provider_error(exc):
                        raise SourceAssetContentTransient(
                            "source provider stream was interrupted"
                        ) from exc
                    raise SourceAssetContentUnavailable(
                        "source provider stream could not be read"
                    ) from exc

            try:
                yield AssetDownloadStream(
                    body=body(), close=close, status_code=stream.status_code,
                    content_type=stream.content_type, headers=stream.headers,
                )
            finally:
                await close()

    @staticmethod
    def _is_transient_provider_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and (
            status_code == 408 or status_code == 429 or 500 <= status_code <= 599
        ):
            return True
        try:
            import httpx
            httpx_transient = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
        except ImportError:
            httpx_transient = ()
        return isinstance(exc, (TimeoutError, ConnectionError, OSError, *httpx_transient))
