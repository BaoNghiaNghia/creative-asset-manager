from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from app.modules.explorer.schema import AssetNode
from app.providers.source_factory import create_source_provider

@dataclass(frozen=True, slots=True)
class FolderEntry:
    id: str
    name: str
    path: str = ""
    parent_id: str | None = None
    @classmethod
    def from_node(cls, node: AssetNode) -> "FolderEntry":
        return cls(id=node.id, name=node.name, path=getattr(node, "path", None) or node.name, parent_id=node.parent_id)

class FolderListingGateway(Protocol):
    async def list_child_folders_page(self, parent_id: str, *, page_token: str | None = None, page_size: int = 100) -> tuple[list[FolderEntry], str | None]: ...
    async def list_all_child_folders(self, parent_id: str, *, page_size: int = 100) -> list[FolderEntry]: ...

class ExplorerFolderListingGateway:
    """Read-only adapter over the existing Explorer source provider contract."""
    def __init__(self, provider: str, access_token: str, *, provider_factory: Callable = create_source_provider):
        self.provider = provider
        self.access_token = access_token
        self.provider_factory = provider_factory
        self._client = None
    async def __aenter__(self) -> "ExplorerFolderListingGateway":
        self._client = self.provider_factory(self.provider, self.access_token)
        await self._client.__aenter__()
        return self
    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, traceback)
            self._client = None
    async def list_child_folders_page(self, parent_id: str, *, page_token: str | None = None, page_size: int = 100):
        if self._client is None:
            raise RuntimeError("folder gateway must be used as an async context manager")
        children, next_token = await self._client.list_children_page(parent_id, folders_only=True, page_token=page_token, page_size=page_size)
        return [FolderEntry.from_node(item) for item in children], next_token
    async def list_all_child_folders(self, parent_id: str, *, page_size: int = 100) -> list[FolderEntry]:
        result: list[FolderEntry] = []
        token = None
        while True:
            page, token = await self.list_child_folders_page(parent_id, page_token=token, page_size=page_size)
            result.extend(page)
            if token is None:
                return result
