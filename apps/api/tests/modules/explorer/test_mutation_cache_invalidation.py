import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.modules.explorer.router import create_folder, delete_item, move_item
from app.modules.explorer.schema import AssetNode


class FakeMutationProvider:
    def __init__(self):
        self.parent = AssetNode(
            id="destination",
            name="Destination",
            kind="folder",
            mime_type="application/vnd.google-apps.folder",
        )
        self.current = AssetNode(
            id="file-a",
            name="asset.png",
            kind="image",
            mime_type="image/png",
            parent_id="old-parent",
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get_node(self, item_id):
        if item_id in {"destination", "parent-a"}:
            return self.parent
        return self.current

    async def create_folder(self, parent_id, name):
        return AssetNode(
            id="new-folder",
            name=name,
            kind="folder",
            mime_type="application/vnd.google-apps.folder",
            parent_id=parent_id,
        )

    async def delete_file(self, item_id):
        return None

    async def move_file(self, item_id, destination_parent_id):
        return self.current.model_copy(
            update={"id": item_id, "parent_id": destination_parent_id}
        )


def _principal():
    return SimpleNamespace(
        membership_id="membership-a",
        effective_roles=("tenant_admin",),
    )


def _context_patches(provider, invalidate):
    return (
        patch(
            "app.modules.explorer.router._source_context",
            new=AsyncMock(
                return_value=("token", "account-a", "tenant-a", "source-a")
            ),
        ),
        patch(
            "app.modules.explorer.router.create_source_provider",
            return_value=provider,
        ),
        patch("app.modules.explorer.router.ViewerFolderScopeService"),
        patch("app.modules.explorer.router._require_viewer_folder_scope"),
        patch(
            "app.modules.explorer.router.invalidate_drive_listings",
            invalidate,
        ),
    )


def test_create_folder_invalidates_destination_listing():
    async def scenario():
        provider = FakeMutationProvider()
        invalidate = Mock()
        patches = _context_patches(provider, invalidate)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await create_folder(
                SimpleNamespace(),
                name="New",
                parent_id="parent-a",
                provider="google-drive",
                session=SimpleNamespace(),
                principal=_principal(),
                external_source_id="source-a",
            )
        invalidate.assert_called_once_with(
            tenant_id="tenant-a",
            external_source_id="source-a",
            parent_id="parent-a",
        )

    asyncio.run(scenario())


def test_delete_invalidates_original_parent_listing():
    async def scenario():
        provider = FakeMutationProvider()
        invalidate = Mock()
        patches = _context_patches(provider, invalidate)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patch("app.modules.explorer.router.is_pure_viewer", return_value=False),
        ):
            await delete_item(
                SimpleNamespace(),
                "file-a",
                provider="google-drive",
                session=SimpleNamespace(),
                principal=_principal(),
                external_source_id="source-a",
            )
        invalidate.assert_called_once_with(
            tenant_id="tenant-a",
            external_source_id="source-a",
            parent_id="old-parent",
        )

    asyncio.run(scenario())


def test_move_invalidates_source_listings_for_old_and_new_parent():
    async def scenario():
        provider = FakeMutationProvider()
        invalidate = Mock()
        patches = _context_patches(provider, invalidate)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await move_item(
                SimpleNamespace(),
                "file-a",
                destination_parent_id="destination",
                provider="google-drive",
                session=SimpleNamespace(),
                principal=_principal(),
                external_source_id="source-a",
            )
        invalidate.assert_called_once_with(
            tenant_id="tenant-a",
            external_source_id="source-a",
        )

    asyncio.run(scenario())
