from collections.abc import Callable
from typing import Protocol

from app.domain.providers.contracts import AssetSourceProvider
from app.modules.explorer.schema import AssetNode


class ExplorerSourceProvider(AssetSourceProvider, Protocol):
    async def __aenter__(self) -> "ExplorerSourceProvider": ...

    async def __aexit__(self, exc_type, exc, traceback) -> None: ...

    async def get_node(self, item_id: str) -> AssetNode: ...

    async def list_children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[AssetNode]: ...


SourceProviderFactory = Callable[[str, str], ExplorerSourceProvider]
