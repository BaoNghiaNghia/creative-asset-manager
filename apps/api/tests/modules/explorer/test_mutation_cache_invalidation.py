import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.modules.authorization.principal import require_authenticated_principal
from app.modules.explorer.router import create_folder, delete_item, move_item, rename_item
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

    async def rename_file(self, item_id, name):
        return self.current.model_copy(update={"id": item_id, "name": name})


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
        patch(
            "app.modules.explorer.router._require_viewer_folder_scope_from_provider",
            new=AsyncMock(),
        ),
    )


def test_create_folder_route_requires_authentication_without_assets_manage_permission():
    dependency = inspect.signature(create_folder).parameters["principal"].default
    assert dependency.dependency is require_authenticated_principal


def test_create_folder_preserves_viewer_folder_scope():
    async def scenario():
        provider = FakeMutationProvider()
        scope_service = Mock()
        access = object()
        scope_service.return_value.access.return_value = access
        require_scope = AsyncMock()
        principal = SimpleNamespace(
            membership_id="viewer-membership",
            effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset(),
        )
        with (
            patch(
                "app.modules.explorer.router._source_context",
                new=AsyncMock(return_value=("token", "account-a", "tenant-a", "source-a")),
            ),
            patch("app.modules.explorer.router.create_source_provider", return_value=provider),
            patch("app.modules.explorer.router.ViewerFolderScopeService", scope_service),
            patch("app.modules.explorer.router._require_viewer_folder_scope_from_provider", require_scope),
            patch("app.modules.explorer.router.invalidate_drive_listings"),
        ):
            await create_folder(
                SimpleNamespace(),
                name="Viewer folder",
                parent_id="parent-a",
                provider="google-drive",
                session=SimpleNamespace(close=Mock()),
                principal=principal,
                external_source_id="source-a",
            )
        scope_service.return_value.access.assert_called_once_with(
            tenant_id="tenant-a",
            membership_id="viewer-membership",
            roles=frozenset({"viewer"}),
            external_source_id="source-a",
        )
        require_scope.assert_awaited_once_with(
            scope_service.return_value,
            tenant_id="tenant-a",
            access=access,
            provider="google-drive",
            token="token",
            folder_id="parent-a",
            allow_root=False,
        )

    asyncio.run(scenario())


def test_create_folder_invalidates_destination_listing():
    async def scenario():
        provider = FakeMutationProvider()
        invalidate = Mock()
        patches = _context_patches(provider, invalidate)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await create_folder(
                SimpleNamespace(),
                name="New",
                parent_id="parent-a",
                provider="google-drive",
                session=SimpleNamespace(close=Mock()),
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


def test_rename_updates_name_and_invalidates_parent_listing_and_breadcrumbs():
    async def scenario():
        provider = FakeMutationProvider()
        invalidate = Mock()
        breadcrumb_invalidate = Mock()
        patches = _context_patches(provider, invalidate)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patch(
                "app.modules.explorer.router.location_breadcrumb_cache.invalidate",
                breadcrumb_invalidate,
            ),
        ):
            result = await rename_item(
                SimpleNamespace(),
                "file-a",
                name="  renamed.png  ",
                provider="google-drive",
                session=SimpleNamespace(),
                principal=_principal(),
                external_source_id="source-a",
            )
        assert result["name"] == "renamed.png"
        invalidate.assert_called_once_with(
            tenant_id="tenant-a",
            external_source_id="source-a",
            parent_id="old-parent",
        )
        breadcrumb_invalidate.assert_called_once_with(
            tenant_id="tenant-a",
            external_source_id="source-a",
        )

    asyncio.run(scenario())
