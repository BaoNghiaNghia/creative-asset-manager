from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domain.providers.contracts import (
    AssetDownloadStream,
    ExternalAssetCandidate,
    GetSourceAssetInput,
    ListSourceChangesInput,
    OpenSourceAssetInput,
    SourceChangePage,
)
from app.modules.explorer.schema import AssetNode


def candidate_from_node(
    node: AssetNode,
    *,
    source_type: str,
    source_id: str,
) -> ExternalAssetCandidate:
    modified_at = node.modified_at.isoformat() if node.modified_at else None
    return ExternalAssetCandidate(
        source_type=source_type,  # type: ignore[arg-type]
        source_id=source_id,
        external_asset_id=node.id,
        filename=node.name,
        mime_type=node.mime_type,
        size_bytes=node.size,
        source_modified_at=modified_at,
        source_metadata=node.model_dump(mode="json"),
    )


class BaseSourceAdapter:
    source_type: str

    def __init__(self, access_token: str, client_factory: Callable[[str], Any]):
        self._access_token = access_token
        self._client_factory = client_factory
        self._client: Any | None = None

    async def __aenter__(self):
        self._client = self._client_factory(self._access_token)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, traceback)
            self._client = None

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("source adapter must be used as an async context manager")
        return self._client

    async def get_node(self, item_id: str) -> AssetNode:
        return await self.client.get(item_id)

    async def list_children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[AssetNode]:
        return await self.client.children(parent_id, folders_only=folders_only)

    async def list_children_page(
        self,
        parent_id: str,
        *,
        folders_only: bool = False,
        page_token: str | None = None,
        page_size: int = 100,
    ) -> tuple[list[AssetNode], str | None]:
        page_reader = getattr(self.client, "children_page", None)
        if page_reader is not None:
            return await page_reader(
                parent_id,
                folders_only=folders_only,
                page_token=page_token,
                page_size=page_size,
            )

        # Keep existing source adapters working while they adopt native page
        # tokens. Google Drive supplies the native cursor above; this fallback
        # preserves the former full-list behavior for legacy adapters.
        children = await self.client.children(parent_id, folders_only=folders_only)
        try:
            start = int(page_token or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid legacy source page token") from exc
        page = children[start : start + page_size]
        next_start = start + len(page)
        next_page_token = str(next_start) if next_start < len(children) else None
        return page, next_page_token

    async def get_asset(self, input: GetSourceAssetInput) -> ExternalAssetCandidate:
        node = await self.get_node(input.external_asset_id)
        return candidate_from_node(
            node,
            source_type=self.source_type,
            source_id=input.source_id,
        )

    async def list_changes(self, input: ListSourceChangesInput) -> SourceChangePage:
        raise NotImplementedError("incremental source sync is introduced in Step 06")

    async def open_download_stream(
        self, input: OpenSourceAssetInput
    ) -> AssetDownloadStream:
        raise NotImplementedError
